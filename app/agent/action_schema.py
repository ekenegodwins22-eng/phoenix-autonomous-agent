from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

ActionName = Literal[
    'goto',
    'click',
    'fill',
    'fill_form',
    'fill_generated',
    'generate_credential',
    'select',
    'press',
    'wait',
    'inspect',
    'mailbox_health',
    'mailbox_session',
    'mailbox_restore_session',
    'mailbox_accounts',
    'mailbox_aliases',
    'mailbox_prepare_email',
    'mailbox_messages',
    'mailbox_get_alias',
    'mailbox_update_alias',
    'mailbox_delete_alias',
    'mailbox_create_alias',
    'mailbox_list_messages',
    'mailbox_get_message',
    'mailbox_fill_verification_code',
    'mailbox_open_verification_link',
    'mailbox_attachment',
    'done',
]

_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    'goto': ('url',),
    'click': ('target',),
    'fill': ('target', 'value'),
    'fill_generated': ('target', 'name'),
    'generate_credential': ('name', 'kind'),
    'select': ('target', 'value'),
    'press': ('target',),
    'mailbox_restore_session': ('alias_email',),
    'mailbox_prepare_email': ('target',),
    'mailbox_messages': ('alias_id',),
    'mailbox_get_alias': ('alias_id',),
    'mailbox_update_alias': ('alias_id', 'fields'),
    'mailbox_delete_alias': ('alias_id',),
    'mailbox_get_message': ('message_id',),
    'mailbox_fill_verification_code': ('target',),
    'mailbox_attachment': ('message_id', 'attachment_id'),
}


class ActionRequest(BaseModel):
    """Validated action boundary for Phoenix's browser and mailbox tools."""

    model_config = ConfigDict(extra='ignore')

    action: ActionName
    url: str | None = None
    target: str | None = None
    value: str | None = None
    name: str | None = None
    kind: str | None = None
    length: int | None = None
    prefix: str | None = None
    milliseconds: int | None = None
    key: str | None = None
    alias_email: str | None = None
    variation_type: str | None = None
    address_type: str | None = None
    gmail_account_id: str | None = None
    alias_id: str | None = None
    fields: dict[str, Any] | None = None
    message_id: str | None = None
    pattern: str | None = None
    attachment_id: str | None = None
    variation_label: str | None = None
    custom_name: str | None = None
    use_primary: bool | None = None
    summary: str | None = None

    @model_validator(mode='after')
    def require_action_fields(self) -> 'ActionRequest':
        missing = []
        for field_name in _REQUIRED_FIELDS.get(self.action, ()):
            value = getattr(self, field_name)
            if value is None or (field_name != 'value' and isinstance(value, str) and not value.strip()):
                missing.append(field_name)
        if missing:
            raise ValueError(f"{self.action} requires: {', '.join(missing)}")
        return self
