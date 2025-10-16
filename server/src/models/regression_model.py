import joblib
import os
import numpy as np

# Path to the model
MODEL_PATH = os.path.join(os.path.dirname(__file__), 'regression_model.joblib')

# Load the model
try:
    model = joblib.load(MODEL_PATH)
except FileNotFoundError:
    model = None
    print(f"Warning: Regression model not found at {MODEL_PATH}")

def predict_fullness(distance: float | None) -> float | None:
    """
    Predicts the fullness percentage using the regression model.
    """
    if model is None or distance is None:
        return None
    
    try:
        # The model expects a 2D array as input
        prediction = model.predict(np.array([[distance]]))
        # Ensure the prediction is within a reasonable range (e.g., 0-100)
        return max(0.0, min(100.0, prediction[0]))
    except Exception as e:
        print(f"Error during regression model prediction: {e}")
        return None
