"""What the two halves of the window catch, named once so they agree.

Image sets live on the RockMaker SMB share. A dropped share raises plain
OSError (for example "Host is down"), not only PlateImagesNotFoundError, so
reading one can fail in a way that saving to the review database cannot.
"""

from __future__ import annotations

from xtalflow.application import ReviewPersistenceError


# Asking the review database for something, or being given something invalid.
STORE_ERRORS = (ValueError, ReviewPersistenceError)

# Reading an image set, which also goes over the network.
IMAGE_SOURCE_ERRORS = (OSError, *STORE_ERRORS)
