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

`handwritten-touching.png` is a 190 × 155 original crop from source coordinates
`(155, 690)` on the second test page. Its model mask has crop-relative origin
`(12, 19)` and confidence `0.929588`. This is a real incorrect union of two
touching balloons: mixed-orientation detector fragments and punctuation
previously disabled visible-seam checks, while the curved seam only partly
matched the model's outer boundary. The expected result is two separate groups,
containing two and three saved detector fragments respectively.
