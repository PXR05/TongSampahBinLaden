import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures
from typing import Optional

# Global model that will be trained from sensor data
regression_model: Optional[LinearRegression] = None
poly_features: Optional[PolynomialFeatures] = None
min_distance: float = 0.0
max_distance: float = 20.0

def train_regression_model(csv_path: str, bin_height_cm: float = 20.0) -> bool:
    """
    Train a regression model from historical sensor data.
    Maps distance (cm) to fullness percentage (0-100%).
    
    Logic: 
    - distance = bin_height_cm → fullness = 0% (empty)
    - distance = 0 cm → fullness = 100% (full)
    """
    global regression_model, poly_features, min_distance, max_distance
    
    try:
        # Read historical data
        df = pd.read_csv(csv_path)
        
        if df.empty or 'distance' not in df.columns:
            print("No distance data available for regression training")
            return False
        
        # Filter valid distance readings (0 to bin_height_cm)
        df = df[(df['distance'] >= 0) & (df['distance'] <= bin_height_cm)].copy()
        
        if len(df) < 5:
            print(f"Not enough data points for regression training (need at least 5, got {len(df)})")
            return False
        
        # Calculate fullness percentage: fullness = (1 - distance/bin_height) * 100
        df['fullness_percent'] = ((bin_height_cm - df['distance']) / bin_height_cm) * 100.0
        
        # Prepare training data
        X = df[['distance']].values
        y = df['fullness_percent'].values
        
        # Use polynomial features for better fitting (degree 2)
        poly_features = PolynomialFeatures(degree=2)
        X_poly = poly_features.fit_transform(X)
        
        # Train the model
        regression_model = LinearRegression()
        regression_model.fit(X_poly, y)
        
        min_distance = df['distance'].min()
        max_distance = df['distance'].max()
        
        print(f"✓ Regression model trained with {len(df)} data points")
        print(f"  Distance range: {min_distance:.2f} - {max_distance:.2f} cm")
        
        return True
        
    except Exception as e:
        print(f"Error training regression model: {e}")
        return False


def predict_fullness(distance: float | None) -> float | None:
    """
    Predicts the fullness percentage using the auto-trained regression model.
    
    Returns:
        Fullness percentage (0-100) or None if model not trained or distance invalid
    """
    if distance is None:
        return None
    
    # Fallback to simple linear calculation if model not trained yet
    if regression_model is None or poly_features is None:
        # Simple inverse relationship: closer to 0 = fuller
        # Assume 20cm bin height as default
        bin_height = 20.0
        if distance < 0:
            return 100.0
        if distance >= bin_height:
            return 0.0
        return ((bin_height - distance) / bin_height) * 100.0
    
    try:
        # Use trained model for prediction
        X = np.array([[distance]])
        X_poly = poly_features.transform(X)
        prediction = regression_model.predict(X_poly)[0]
        
        # Clamp to reasonable range
        return max(0.0, min(100.0, float(prediction)))
        
    except Exception as e:
        print(f"Error during regression prediction: {e}")
        # Fallback to simple calculation
        bin_height = 20.0
        if distance < 0:
            return 100.0
        if distance >= bin_height:
            return 0.0
        return ((bin_height - distance) / bin_height) * 100.0
