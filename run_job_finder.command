#!/bin/bash
# Double-click launcher for the Job Finder app.
cd ~/Documents/ai/crewai-starter
source venv/bin/activate
pip install -q -r requirements.txt
streamlit run streamlit_app.py
