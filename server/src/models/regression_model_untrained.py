# models/regression_model.py

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
    df_list = [pd.read_csv(file) for file in file_paths]
    df = pd.concat(df_list, ignore_index=True)

    # --- THIS IS THE NEW LINE ---
    # Filter data to match the 20cm box height
    df = df[(df['distance'] <= 20) & (df['distance'] >= 0)]
    
    df['serverTimestamp'] = pd.to_datetime(df['serverTimestamp'])
    df = df.sort_values(by='serverTimestamp')
    df['time_elapsed'] = (df['serverTimestamp'] - df['serverTimestamp'].min()).dt.total_seconds()
    
    X = df[['time_elapsed']]
    y = df['distance']

    regression_model = LinearRegression()
    regression_model.fit(X, y)
    return regression_model

def predict_time_to_full(model, current_time_elapsed, full_threshold_cm=5):
    """
    Predicts the time remaining until the trash can is full.
    """
    m = model.coef_[0]
    c = model.intercept_
    if m >= 0:
        return float('inf')
    predicted_time_to_full_seconds = (full_threshold_cm - c) / m
    time_remaining_seconds = predicted_time_to_full_seconds - current_time_elapsed
    return time_remaining_seconds / 3600

def evaluate_regression_model(file_paths):
    """
    Splits data, trains the model, and evaluates its performance.
    """
    print("Evaluating Regression Model (for 20cm box)...")
    df_list = [pd.read_csv(file) for file in file_paths]
    df = pd.concat(df_list, ignore_index=True)
    
    # --- THIS IS THE NEW LINE ---
    # Filter data to match the 20cm box height
    df = df[(df['distance'] <= 20) & (df['distance'] >= 0)]

    df['serverTimestamp'] = pd.to_datetime(df['serverTimestamp'])
    df = df.sort_values(by='serverTimestamp')
    df['time_elapsed'] = (df['serverTimestamp'] - df['serverTimestamp'].min()).dt.total_seconds()
    X = df[['time_elapsed']]
    y = df['distance']

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    model = LinearRegression()
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    
    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)

    # Create a directory to store models if it doesn't exist
    os.makedirs('saved_models', exist_ok=True)
    # Save the model object to a file
    model_filename = 'saved_models/regression_model.joblib'
    joblib.dump(model, model_filename)
    print(f"\n  - Model saved successfully to '{model_filename}'")

    print(f"  - Mean Absolute Error (MAE): {mae:.2f} cm")
    print(f"  - Root Mean Squared Error (RMSE): {rmse:.2f} cm")
    print(f"  - R-squared (R²): {r2:.2f}")
    print("-" * 20)
    return model