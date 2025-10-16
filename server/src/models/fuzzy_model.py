# models/fuzzy_model.py

import numpy as np


def triangular_mf(x, a, b, c):
    return max(0, min((x - a) / (b - a) if b - a != 0 else 1, (c - x) / (c - b) if c - b != 0 else 1))

# --- MEMBERSHIP FUNCTIONS UPDATED FOR 20CM HEIGHT ---
def distance_full_mf(x):
    # Full is when distance is very low (e.g., 0-4cm)
    return triangular_mf(x, -1, 0, 4)

def distance_medium_mf(x):
    # Medium is in the middle of the range (e.g., 3-12cm)
    return triangular_mf(x, 5, 8, 12)

def distance_empty_mf(x):
    # Empty is when distance is high, near 20cm (e.g., 10-20cm)
    return triangular_mf(x, 13, 20, 21)
# --- END OF UPDATES ---

def fullness_high_mf(x):
    return triangular_mf(x, 70, 100, 101)

def fullness_medium_mf(x):
    return triangular_mf(x, 30, 50, 70)

def fullness_low_mf(x):
    return triangular_mf(x, -1, 0, 30)

def compute_fullness(distance_input):
    fuzzified_distance = {
        'full': distance_full_mf(distance_input),
        'medium': distance_medium_mf(distance_input),
        'empty': distance_empty_mf(distance_input)
    }
    rule1_activation = fuzzified_distance['full']
    rule2_activation = fuzzified_distance['medium']
    rule3_activation = fuzzified_distance['empty']
    fullness_universe = np.arange(0, 101, 1)
    fullness_high_clipped = np.fmin(rule1_activation, [fullness_high_mf(x) for x in fullness_universe])
    fullness_medium_clipped = np.fmin(rule2_activation, [fullness_medium_mf(x) for x in fullness_universe])
    fullness_low_clipped = np.fmin(rule3_activation, [fullness_low_mf(x) for x in fullness_universe])
    aggregated = np.fmax(fullness_high_clipped, np.fmax(fullness_medium_clipped, fullness_low_clipped))
    numerator = np.sum(fullness_universe * aggregated)
    denominator = np.sum(aggregated)
    if denominator == 0:
        return 0
    return numerator / denominator

def validate_fuzzy_system():
    print("\nValidating Fuzzy Logic System (for 20cm box)...")
    # --- UPDATED PLOT RANGE ---
    distances = np.arange(0, 21, 1) # Plot from 0cm to 20cm
    fullness_outputs = [compute_fullness(d) for d in distances]

    
