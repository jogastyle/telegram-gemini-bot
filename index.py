import os
import sys

# api folder ka path system mein add karein
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))

# webhook module se Flask app import karein
from index import app

# Vercel entrypoint bind
app = app
