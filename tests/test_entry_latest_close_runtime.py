"""Current three-closed-bar policy must return a valid signal, not NameError."""
import pandas as pd
import pytest
from core.services.entry_contract import evaluate_entry_contract


