"""Points projection model.

STATUS: stub. Planned shape (see the planning doc's Model approach
section): a LightGBM point estimate + quantile models (p10/p25/p50/p75/p90),
same family as the K Board's kproj/model/, trained on player_game_logs.

Feature candidates to implement first, roughly in order of expected
importance for points specifically:
  - projected minutes (biggest NBA-specific variance driver — no MLB
    analogue; may need to be its own small model rather than one feature)
  - recent scoring rate (5/10/20-game recency-weighted average)
  - season-long baseline rate, to regress small samples toward
  - opponent defensive rating / pace
  - usage rate and role stability
  - home/away, rest days, back-to-back
  - blowout risk (spread magnitude)
"""


def train(con, quick: bool = False):
    raise NotImplementedError("model.predict.train: build the LightGBM pipeline here")


def project_date(con, date_s: str):
    raise NotImplementedError("model.predict.project_date: score today's slate here")
