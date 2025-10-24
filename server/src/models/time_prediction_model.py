# models/time_prediction_model.py

import pandas as pd
from sklearn.linear_model import LinearRegression
import os
from datetime import datetime
from typing import Optional, Tuple

# Global variables for the model
time_prediction_model: Optional[LinearRegression] = None
model_start_time: Optional[datetime] = None
model_last_reading_time: Optional[datetime] = None
model_last_distance: Optional[float] = None
model_data_points: int = 0

def train_fullness_prediction_model(file_paths: list[str], min_data_points: int = 10) -> bool:
    """
    Trains a linear regression model to predict when trash can will be full.
    Uses time elapsed vs distance to predict future fullness.
    
    Returns:
        True if model was successfully trained, False otherwise
    """
    global time_prediction_model, model_start_time, model_last_reading_time, model_last_distance, model_data_points
    
    if not file_paths:
        print("No data files provided for time prediction training")
        return False
    
    try:
        # Load all CSV files
        df_list = []
        for file_path in file_paths:
            if os.path.exists(file_path):
                df_list.append(pd.read_csv(file_path))
        
        if not df_list:
            print("No valid data files found for time prediction training")
            return False
            
        df = pd.concat(df_list, ignore_index=True)

        if df.empty or 'distance' not in df.columns or 'serverTimestamp' not in df.columns:
            print("Missing required columns in data for time prediction")
            return False

        # Filter valid distance readings (0-20cm for typical bin)
        df = df[(df['distance'] <= 20) & (df['distance'] >= 0)].copy()
        
        if len(df) < min_data_points:
            print(f"Not enough data points for time prediction (need {min_data_points}, got {len(df)})")
            return False

        # Parse timestamps
        df['serverTimestamp'] = pd.to_datetime(df['serverTimestamp'])
        df = df.sort_values(by='serverTimestamp')
        
        # Calculate time elapsed from first reading (in seconds)
        start_time = df['serverTimestamp'].min()
        df['time_elapsed'] = (df['serverTimestamp'] - start_time).dt.total_seconds()
        
        # Prepare training data: time_elapsed -> distance
        X = df[['time_elapsed']].values
        y = df['distance'].values

        # Train linear regression model
        model = LinearRegression()
        model.fit(X, y)
        
        # Store globally
        time_prediction_model = model
        model_start_time = start_time.to_pydatetime()
        model_last_reading_time = df['serverTimestamp'].max().to_pydatetime()
        model_last_distance = df.iloc[-1]['distance']
        model_data_points = len(df)
        
        slope = model.coef_[0]
        intercept = model.intercept_
        
        print(f"✓ Time prediction model trained with {len(df)} data points")
        print(f"  Model equation: distance = {slope:.6f} * time + {intercept:.2f}")
        print(f"  Start time: {model_start_time}")
        
        return True
        
    except Exception as e:
        print(f"Error training time prediction model: {e}")
        return False


def predict_time_to_full(full_threshold_cm: float = 5.0) -> Optional[float]:
    """
    Predicts the time remaining (in hours) until the trash can reaches the full threshold.
    
    Args:
        full_threshold_cm: Distance threshold considered "full" (default 5cm)
    
    Returns:
        Hours until full, or None if prediction not possible
        Returns float('inf') if bin is not filling (distance increasing or static)
        Returns 0 if already full
    """
    global time_prediction_model, model_start_time, model_last_reading_time, model_last_distance
    
    if time_prediction_model is None or model_start_time is None or model_last_reading_time is None:
        return None

    try:
        slope = time_prediction_model.coef_[0]
        intercept = time_prediction_model.intercept_

        # If slope >= 0, distance is increasing or static (not filling up)
        if slope >= 0:
            return float('inf')

        # Use last reading as current state
        current_distance = model_last_distance
        
        # If already at or below threshold, return 0
        if current_distance <= full_threshold_cm:
            return 0.0

        # Calculate time elapsed from model start to last reading
        if model_start_time.tzinfo is not None:
            from datetime import timezone
            if model_last_reading_time.tzinfo is None:
                model_last_reading_time = model_last_reading_time.replace(tzinfo=timezone.utc)
        
        time_elapsed_at_last_reading = (model_last_reading_time - model_start_time).total_seconds()
        
        # Calculate when distance will reach full_threshold_cm
        # distance = slope * time + intercept
        # full_threshold_cm = slope * time_to_full + intercept
        # time_to_full = (full_threshold_cm - intercept) / slope
        time_to_full_seconds = (full_threshold_cm - intercept) / slope
        
        # Calculate remaining time from last reading
        time_remaining_seconds = time_to_full_seconds - time_elapsed_at_last_reading
        
        if time_remaining_seconds < 0:
            return 0.0  # Already past the full threshold
        
        # Convert to hours
        hours = time_remaining_seconds / 3600.0
        
        return max(0.0, hours)  # Ensure non-negative
        
    except Exception as e:
        print(f"Error predicting time to full: {e}")
        return None


def get_model_info() -> dict:
    """
    Returns information about the current time prediction model.
    """
    global time_prediction_model, model_start_time, model_last_reading_time, model_last_distance, model_data_points
    
    if time_prediction_model is None:
        return {
            "trained": False,
            "message": "Model not trained yet"
        }
    
    return {
        "trained": True,
        "data_points": model_data_points,
        "start_time": model_start_time.isoformat() if model_start_time else None,
        "last_reading_time": model_last_reading_time.isoformat() if model_last_reading_time else None,
        "last_distance": model_last_distance,
        "slope": float(time_prediction_model.coef_[0]),
        "intercept": float(time_prediction_model.intercept_),
        "filling": time_prediction_model.coef_[0] < 0
    }
