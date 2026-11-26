"""Useful validation diagnostics without echoing request values or dynamic keys."""

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, TypeAdapter, ValidationError


class ValidationIssue(BaseModel):
    loc: tuple[str | int, ...]
    type: str


_VALIDATION_MESSAGES = {
    "missing": "缺少必填字段。",
    "extra_forbidden": "包含不支持的字段。",
    "string_type": "应填写文本。",
    "string_too_short": "文本长度不足。",
    "string_too_long": "文本超过允许长度。",
    "int_type": "应填写整数。",
    "int_parsing": "无法解析为整数。",
    "float_type": "应填写数值。",
    "float_parsing": "无法解析为数值。",
    "bool_type": "应填写布尔值。",
    "bool_parsing": "无法解析为布尔值。",
    "dict_type": "应提供对象。",
    "list_type": "应提供列表。",
    "model_attributes_type": "应提供包含字段的对象。",
    "enum": "取值不在允许范围内。",
    "literal_error": "取值不在允许范围内。",
    "greater_than": "数值必须大于允许下限。",
    "greater_than_equal": "数值不能小于允许下限。",
    "less_than": "数值必须小于允许上限。",
    "less_than_equal": "数值不能大于允许上限。",
    "json_invalid": "JSON 格式不正确。",
}


def validation_details(exc: RequestValidationError | ValidationError) -> list[dict[str, object]]:
    issues = TypeAdapter(list[ValidationIssue]).validate_python(exc.errors())
    result: list[dict[str, object]] = []
    for issue in issues:
        location = list(issue.loc)
        # Header names and unknown object keys are user input, just like their values.
        if "headers" in location:
            index = location.index("headers")
            location = location[: index + 1] + (["<key>"] if len(location) > index + 1 else [])
        elif issue.type == "extra_forbidden" and location:
            location[-1] = "<extra>"
        result.append(
            {
                "loc": location,
                "type": issue.type,
                "msg": _VALIDATION_MESSAGES.get(issue.type, "字段值不符合要求。"),
            }
        )
    return result
