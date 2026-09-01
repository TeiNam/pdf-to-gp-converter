import os
import sys
from typing import Dict, List, Optional, Union, Any

# Add PyGuitarPro to the system path
script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
repo_root = os.path.dirname(script_dir)
guitar_pro_path = os.path.join(repo_root, 'PyGuitarPro')

# 소스 체크아웃이 있으면 우선 사용하고, 없으면 설치된 pyguitarpro 패키지를 쓴다
if os.path.exists(guitar_pro_path) and guitar_pro_path not in sys.path:
    sys.path.insert(0, guitar_pro_path)

# Import PyGuitarPro modules
try:
    import guitarpro as gp
except ImportError as e:
    print(f"Error importing guitarpro module: {e}")
    print(f"Make sure the PyGuitarPro directory exists at {guitar_pro_path}")
    raise

from guitarpro.models import (
    Song, Track, Measure, MeasureHeader, Voice, Beat, Note, 
    Duration, TimeSignature, KeySignature, TripletFeel
)

from guitarpro import parse

class GuitarProMixin:
    """Mixin class providing basic Guitar Pro functionality."""
    
    def __init__(self):
        """Initialize the Guitar Pro controller."""
        self.current_song = None
        
    def _ensure_song_loaded(self):
        """Ensure a song is loaded before performing operations."""
        if not self.current_song:
            raise ValueError("No song is currently loaded") 