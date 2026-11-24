"""Common output storage, independent of CAD input and repository location."""
from pathlib import Path
import os
from assembly_v12.contracts import read,location

ROOT=Path(__file__).resolve().parents[2]

def data_root():
    config=ROOT/'config/local.storage.v2.json'
    value=os.environ.get('ASSEMBLY_DATA_ROOT')
    if not value and config.is_file():value=read(config).get('data_root')
    # location rejects E:, network shares, traversal and linked directories.
    return location(value or ROOT)

def run_root():return data_root()/'runs'
def memory_root():return data_root()/'runtime/memory/assembly_v2'
def upload_root():return data_root()/'runtime/ui_inputs'
