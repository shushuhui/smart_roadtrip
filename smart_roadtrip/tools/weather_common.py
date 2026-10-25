"""天气工具共用的逐日预报模型。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class DailyForecast(BaseModel):
    """与供应商无关的逐日预报。"""

    date: date
    condition: str
    temp_low_c: float
    temp_high_c: float
    precipitation_probability: float = Field(ge=0.0, le=1.0)
    warning: str | None = None
