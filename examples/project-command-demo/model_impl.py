"""Editable feature representation for the project-owned classifier."""
import numpy as np

def features(x):
    # Initial linear baseline. A successor must justify its own intervention.
    return np.column_stack((x[:,0],x[:,1],np.ones(len(x))))
