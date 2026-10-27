"""Image generation and editing through ComfyUI.

Will It Run does not ship a diffusion model and does not run one itself: ComfyUI does,
on the same PC, and this package is the bridge. ``ImageStudio`` owns the request, the
ComfyUI graph it becomes, the wait and the picture that comes back.
"""

from agentfit.imagegen.catalog import ModelProfile, profiles
from agentfit.imagegen.comfy import ComfyClient, ComfyError, ComfyUnavailable
from agentfit.imagegen.service import GenerationRequest, ImageStudio, StudioError

__all__ = [
    "ComfyClient",
    "ComfyError",
    "ComfyUnavailable",
    "GenerationRequest",
    "ImageStudio",
    "ModelProfile",
    "StudioError",
    "profiles",
]
