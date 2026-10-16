"""What reading an image set can fail with.

Image sets live on the RockMaker SMB share. A dropped share raises plain
OSError (for example "Host is down"), not only PlateImagesNotFoundError.
"""

from __future__ import annotations

from xtalflow.application import ReviewPersistenceError


IMAGE_SOURCE_ERRORS = (ValueError, OSError, ReviewPersistenceError)
