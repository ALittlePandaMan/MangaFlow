# Handwritten balloon regression fixture

`handwritten-neighbors.png` is a 135 × 327 original-image crop provided for
debugging MangaFlow, from source coordinates `(310, 340)`. It contains the
reported overlapping handwritten columns and the separate balloon above them.
The two `*-mask.png` files are the actual bubble segmentation masks, unchanged.
Their crop-relative origins are upper `(40, 15)` and lower `(15, 153)`; model
confidences were respectively `0.782587` and `0.654595`.

The test replays original detector polygons without loading model weights,
calling external services, accessing a user database, or modifying project data.
Expected grouping is two upper columns and three lower columns, separately.
It also simulates a model mask incorrectly joining these adjacent balloons.
