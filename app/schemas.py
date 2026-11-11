from typing import Any, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterInput(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]{2,31}$")
    email: EmailStr
    phone: str = Field(min_length=8, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    terms_accepted: bool = False
    privacy_acknowledged: bool = False

    @field_validator("username")
    @classmethod
    def username_format(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("phone")
    @classmethod
    def phone_format(cls, value: str) -> str:
        value = value.strip().replace(" ", "")
        if not value.startswith("+") or not value[1:].isdigit():
            raise ValueError("Use international phone format, e.g. +233XXXXXXXXX")
        return value


class LoginInput(BaseModel):
    identifier: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class VerifyInput(BaseModel):
    channel: Literal["email", "sms"]
    code: str = Field(min_length=6, max_length=6)




class PolicyAcceptanceInput(BaseModel):
    terms_accepted: bool = False
    privacy_acknowledged: bool = False


class ChatInput(BaseModel):
    message: str = Field(min_length=2, max_length=12000)
    project_context: str | None = Field(default=None, max_length=6000)
    online_search: bool = False


class SampleSizeInput(BaseModel):
    population: int = Field(gt=0)
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1)
    margin_of_error: float = Field(default=0.05, gt=0, lt=0.5)
    population_proportion: float = Field(default=0.5, gt=0, lt=1)
    nonresponse_rate: float = Field(default=0, ge=0, lt=0.9)
    design_effect: float = Field(default=1, gt=0, le=10)


class LandUseInput(BaseModel):
    baseline_year: int
    current_year: int
    baseline_areas: dict[str, float]
    current_areas: dict[str, float]
    area_unit: Literal["ha", "km2", "m2"] = "ha"


class DatasetInput(BaseModel):
    rows: list[dict[str, Any]] = Field(min_length=3, max_length=50000)


class CorrelationInput(DatasetInput):
    x: str
    y: str
    method: Literal["pearson", "spearman"] = "pearson"


class RegressionInput(DatasetInput):
    dependent: str
    independent: list[str] = Field(min_length=1, max_length=20)


class DevelopmentAppraisalInput(BaseModel):
    gross_development_value: float = Field(gt=0)
    construction_costs: float = Field(ge=0)
    professional_fees: float = Field(default=0, ge=0)
    infrastructure_costs: float = Field(default=0, ge=0)
    finance_costs: float = Field(default=0, ge=0)
    other_costs: float = Field(default=0, ge=0)
    developer_profit_percent: float = Field(default=15, ge=0, le=100)
    acquisition_cost_percent_of_land: float = Field(default=0, ge=0, le=100)
    site_area_sqm: float = Field(default=0, ge=0)


class WeightedOverlayInput(BaseModel):
    weights: dict[str, float]
    sites: dict[str, dict[str, float]]
    minimum_score: float = Field(default=1, ge=0)
    maximum_score: float = Field(default=5, gt=0)


class PropertyMetricsInput(BaseModel):
    market_value: float = Field(gt=0)
    site_area_sqm: float = Field(default=0, ge=0)
    annual_gross_rent: float = Field(default=0, ge=0)
    annual_operating_costs: float = Field(default=0, ge=0)


class GSSQueryInput(BaseModel):
    topic: str = Field(default="Population", min_length=2, max_length=80)
    table: str = Field(min_length=4, max_length=100)
    # Values may be category labels or PxWeb category codes. Omit a dimension to select all.
    selections: dict[str, list[str]] = Field(default_factory=dict)
