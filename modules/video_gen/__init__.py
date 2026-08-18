"""Module video_gen -- sinh video từ prompt.

Cửa vào công khai của module chỉ gồm bấy nhiêu đây. Mọi thứ khác là nội bộ và
có thể đổi mà không ảnh hưởng ai.
"""

from modules.video_gen.config import VideoGenConfig
from modules.video_gen.models import VideoArtifact, VideoSpec
from modules.video_gen.module import VideoGenModule

__all__ = ["VideoGenConfig", "VideoGenModule", "VideoSpec", "VideoArtifact"]
