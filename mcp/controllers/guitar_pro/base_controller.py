from typing import Dict, List, Optional, Union, Any

# pyguitarpro 는 pyproject.toml 의 의존성이다. upstream 은 레포 옆의 PyGuitarPro
# 소스 체크아웃을 sys.path 에 끼워넣었는데, 파일 깊이에 의존하는 계산이라 폴더를
# 옮길 때마다 깨진다. 설치된 패키지를 그대로 쓴다.
import guitarpro as gp

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