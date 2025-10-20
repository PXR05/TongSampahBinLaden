# models/time_prediction_model.py

import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import numpy as np
import joblib
import os

def train_fullness_prediction_model(file_paths):
    """
    Trains a linear regression model to predict trash can fullness.
    """
    if not file_paths:
        raise ValueError("No data files provided for training.")
        
    df_list = [pd.read_csv(file) for file in file_paths]
    df = pd.concat(df_list, ignore_index=True)

    if df.empty:
        raise ValueError("Historical data is empty. Cannot train model.")

    # Filter data to match the 20cm box height
    df = df[(df['distance'] <= 20) & (df['distance'] >= 0)]
    
    df['serverTimestamp'] = pd.to_datetime(df['serverTimestamp'])
    df = df.sort_values(by='serverTimestamp')
    
    if len(df) < 2:
        raise ValueError("Not enough data points to calculate time elapsed. At least 2 are required.")

    df['time_elapsed'] = (df['serverTimestamp'] - df['serverTimestamp'].min()).dt.total_seconds()
    
    X = df[['time_elapsed']]
    y = df['distance']

    regression_model = LinearRegression()
    regression_model.fit(X, y)
    
    # Store the start time with the model
    start_time = df['serverTimestamp'].min()
    
    # Create a directory to store models if it doesn't exist
    os.makedirs('server/src/models/saved_models', exist_ok=True)
    model_filename = 'server/src/models/saved_models/time_prediction_model.joblib'
    
    # Save the model and the start time
    joblib.dump({'model': regression_model, 'start_time': start_time}, model_filename)
    print(f"\n  - Model saved successfully to '{model_filename}'")
    
    return regression_model, start_time

def predict_time_to_full(model, start_time, full_threshold_cm=5):
    """
    Predicts the time remaining until the trash can is full.
    """
    if model is None or start_time is None:
        return None

    m = model.coef_[0]
    c = model.intercept_

    # If the slope is non-negative, the trash is not filling up, or it's static.
    if m >= 0:
        return float('inf') # Represents infinite time to full

    # Calculate the time elapsed from the start to now
    current_time_elapsed = (pd.Timestamp.now(tz=start_time.tz) - start_time).total_seconds()

    # Predict the time in seconds from the start when the can will be full
    predicted_time_to_full_seconds = (full_threshold_cm - c) / m
    
    # Calculate remaining time
    time_remaining_seconds = predicted_time_to_full_seconds - current_time_elapsed
    
    if time_remaining_seconds < 0:
        return 0 # Already full or past due

    return time_remaining_seconds / 3600 # Convert to hours
