#!/bin/bash
source .xv_venv/bin/activate
export LD_LIBRARY_PATH=/opt/python37/lib
python main.py
deactivate
