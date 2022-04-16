# A BERT model for toxic comment sentiment analysis
# CAP6640 - Spring 2022  
#   
# Portions of this code are modified from this tutorial:
# https://skimai.com/fine-tuning-bert-for-sentiment-analysis/

import os
import torch
import numpy as np
import torch.nn as nn
from tqdm import tqdm
from torch.optim import AdamW
import torch.nn.functional as F
from transformers import BertTokenizer
from utils.roc_auc import evaluate_roc
from data.toxic_dataset import ToxicDataset
from utils.earlystopping import EarlyStopping
from models.bertclassifier import BertClassifier
from sklearn.model_selection import train_test_split
from utils.text_processing import bert_text_formatting
from transformers import get_linear_schedule_with_warmup
import utils.utils as utils
 
# Preprocesses the data provided into tokens and encodes them with BERT'S
# pretrained tokenizer and returns token_ids and attention masks
def preprocess(data, tokenizer, name):

    token_ids = []
    attention_masks = []

    with tqdm(data) as tdata:

        tdata.set_description('{} Text'.format(name))
        
        # For each comment in the data, encode the comment
        # with pretrained BERT tokenizer
        for comment in tdata:
            encoded = tokenizer.encode_plus(
                text=bert_text_preprocessing(comment),
                max_length=256, # Truncates only 277 comments 
                padding='max_length',
                truncation='longest_first',
                return_attention_mask=True
                )

            token_ids.append(encoded.get('input_ids'))
            attention_masks.append(encoded.get('attention_mask'))

    return token_ids, attention_masks

# Trains the BERT model until early stopping is reached
def train(model, train_dataloader, device, criterion, optimizer, scheduler):

    # Set model to train mode before each epoch
    model.train()

    train_loss= 0.0

    # Iterate over entire training samples (1 epoch)
    with tqdm(train_dataloader, unit='batch') as tepoch:
        for step, batch in enumerate(tepoch):

            # Shows the changes in training loss over batches
            current_loss = train_loss / (step + 1)
            tepoch.set_description('Training {:.4f}'.format(current_loss))
            
            token_ids, masks, labels = batch
            
            # Push data/label to correct device
            token_ids = token_ids.to(device)
            masks = masks.to(device)
            labels = labels.to(device)

            # Reset model gradients Avoids grad accumulation
            model.zero_grad()

            # Do forward pass for current set of data
            output = model(token_ids, masks)

            # Compute loss based on criterion
            loss = criterion(output, labels)

            # Update the running training loss
            train_loss += loss.item()

            # Computes gradient based on final loss
            loss.backward()

            # Clips the gradients to 1.0
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)

            # Computes gradient based on final loss
            optimizer.step()
            scheduler.step()

    # Compute the average loss
    average_loss = train_loss / len(train_dataloader)

    print('Train Loss: {:.4f}'.format(average_loss))


# Validates the trained BERT model on unseen an dataset
def validate(model, val_dataloader, device, criterion):

    # Set model to eval mode before each epoch
    model.eval()

    validation_loss = 0
    validation_accuracy = 0

    # Iterate over entire validation samples (1 epoch)
    with tqdm(val_dataloader, unit='batch') as tepoch:
        for step, batch in enumerate(tepoch):
            
            # Shows the changes in training loss over batches
            current_loss = validation_loss / (step + 1)
            tepoch.set_description('Validation {:.4f}'.format(current_loss))

            token_ids, masks, labels = batch
            
            # Push data/label to correct device
            token_ids = token_ids.to(device)
            masks = masks.to(device)
            labels = labels.to(device)

            # Do forward pass for current set of data
            with torch.no_grad():
                output = model(token_ids, masks)

            # Compute loss based on criterion
            loss = criterion(output, labels)

            # Update the running validation loss
            validation_loss += loss.item()

            predictions = torch.argmax(output, dim=1).flatten()

            # Update the running accuracy
            accuracy = (predictions == labels).cpu().numpy().mean() * 100
            validation_accuracy += accuracy

    # Compute the average loss and accuracy
    validation_loss = validation_loss / len(val_dataloader)
    validation_accuracy = validation_accuracy / len(val_dataloader)

    print('Validation Loss: {:.4f}'.format(validation_loss))
    print('Validation Accuracy: {:.1f}'.format(validation_accuracy))

    return validation_loss

# Makes predictions on a test set
def predict(model, test_dataloader, device):

    # Set model to eval mode before each epoch
    model.eval()

    predictions = []

    # Iterate over entire set of test samples 
    with tqdm(test_dataloader, unit='batch') as tepoch:
        for batch in tepoch:
            tepoch.set_description('Prediction')

            token_ids, masks, labels = batch
                
            token_ids = token_ids.to(device)
            masks = masks.to(device)
            labels = labels.to(device)

            # Run through model to get predictions
            with torch.no_grad():
                output = model(token_ids, masks)

            predictions.append(output)
    
    # Get probabilities for the AUC score
    predictions = torch.cat(predictions, dim=0)
    predictions = F.softmax(predictions, dim=1).cpu().numpy()

    return predictions

def run_model():
        
    utils.initial_setup()
    device = utils.get_device()

    # Parameters for model
    epochs = 100
    batch_size = 16
    tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
    criterion = nn.CrossEntropyLoss()
    early_stopping = EarlyStopping(patience=5)
    model_path = '../models/bert_model.pt'

    model = BertClassifier()
    model.to(device)
    optimizer = AdamW(model.parameters(), lr=1e-5)

    # Load the training and test data
    train_data = ToxicDataset(train_split=True)
    test_data = ToxicDataset(train_split=False)
    
    X, y = train_data.get_values()
    X_test, y_test = test_data.get_values()

    # Split train data into train and validation sets 
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.1)    
   
    # Preprocess the data into BERT token ids and masks
    print('Text preprocessing')

    train_inputs, train_masks = preprocess(X_train, tokenizer, 'Train')
    val_inputs, val_masks = preprocess(X_val, tokenizer, 'Validation')
    test_inputs, test_masks = preprocess(X_test, tokenizer, 'Test')

    print('Finished text preprocessing')

    # Create the tensors and dataloaders for training
    print('Creating tensors and dataloaders')
    train_tensors = create_tensors(train_inputs, train_masks, y_train)
    val_tensors = create_tensors(val_inputs, val_masks, y_val)
    test_tensors = create_tensors(test_inputs, test_masks, y_test)

    train_dataloader = get_dataloader(train_tensors, batch_size, train=True)
    val_dataloader = get_dataloader(val_tensors, batch_size)
    test_dataloader = get_dataloader(test_tensors, batch_size)
    
    print('Finished creating tensors and dataloaders')

    print('Start Training')

    total_steps = len(train_dataloader) * epochs

    scheduler = get_linear_schedule_with_warmup(optimizer,
                                                num_warmup_steps=0,
                                                num_training_steps=total_steps)

    min_loss = np.inf

    for epoch in range(epochs):
        print(f'Epoch {epoch + 1}')

        train(model, train_dataloader, device, criterion, optimizer, scheduler)

        loss = validate(model, val_dataloader, device, criterion)
        ''' 
        if loss < min_loss:
            min_loss = loss
            print('Saving best model')
            torch.save(model.state_dict(), model_path)
        ''' 
    
        early_stopping(loss)
        if early_stopping.stop:
            break
        
    print('Finished Training')
    
    '''
    print('Start Prediction')
    
    if os.path.exists(model_path):
        model.load_state_dict(torch.load(model_path))

    # Compute predicted probabilities on the test set
    probabilities = predict(model, test_dataloader, device)

    print('Finished Prediction')

    # Evaluate the Bert classifier
    evaluate_roc(probabilities, y_test, 'BERT')
    '''
