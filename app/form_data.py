"""DOM-field classification and synthetic, non-real test data for authorized forms."""
from __future__ import annotations
import re
import secrets
from datetime import date, timedelta


def synthetic_profile() -> dict[str, str]:
    suffix = secrets.token_hex(4)
    return {
        'first_name': 'Test', 'middle_name': 'Example', 'last_name': 'User',
        'full_name': 'Test Example User', 'username': f'test_user_{suffix}',
        'email': f'test-user-{suffix}@example.test', 'phone': '+1555010' + str(int(suffix[:4], 16) % 10000).zfill(4),
        'company': 'Example Test Company', 'job_title': 'Test User',
        'address': '123 Example Street', 'city': 'Testville', 'state': 'CA', 'country': 'US', 'postal_code': '90210',
        'website': 'https://example.test', 'description': 'Synthetic test data for authorized form testing.',
        'search': 'example test', 'password': 'Test-' + suffix + '-Aa1!',
        'date': '1990-01-15', 'number': '1',
    }


def _text(field: dict) -> str:
    return ' '.join(str(field.get(key) or '') for key in ('label','placeholder','name','id','autocomplete','aria_label')).lower()


def classify_field(field: dict) -> str | None:
    typ = str(field.get('type') or 'text').lower()
    text = _text(field)
    if field.get('disabled') or field.get('readonly') or typ in {'hidden','submit','button','reset','file','image'}:
        return None
    if field.get('tag') == 'select': return 'select'
    if typ == 'email' or 'email' in text: return 'email'
    if typ == 'password' or 'password' in text: return 'password'
    if typ == 'tel' or any(x in text for x in ('phone','telephone','mobile')): return 'phone'
    if typ == 'url' or any(x in text for x in ('website','homepage','url')): return 'website'
    if typ == 'date' or any(x in text for x in ('date of birth','birth date','birthday','dob')): return 'date'
    if typ in {'number','range'} or any(x in text for x in ('quantity','amount','age','number')): return 'number'
    if typ in {'checkbox','radio'}: return typ
    if any(x in text for x in ('first name','given name','forename')): return 'first_name'
    if any(x in text for x in ('middle name','middle initial')): return 'middle_name'
    if any(x in text for x in ('last name','surname','family name')): return 'last_name'
    if any(x in text for x in ('full name','your name','name')): return 'full_name'
    if any(x in text for x in ('user name','username','login')): return 'username'
    if any(x in text for x in ('company','organization','employer')): return 'company'
    if any(x in text for x in ('job title','occupation','role')): return 'job_title'
    if any(x in text for x in ('address','street')): return 'address'
    if any(x in text for x in ('city','town')): return 'city'
    if any(x in text for x in ('state','province','region')): return 'state'
    if any(x in text for x in ('country','nation')): return 'country'
    if any(x in text for x in ('zip','postal')): return 'postal_code'
    if any(x in text for x in ('search','query')): return 'search'
    if field.get('tag') == 'textarea' or any(x in text for x in ('description','comment','message','about')): return 'description'
    return None


def constrained_value(field: dict, kind: str, profile: dict[str, str]) -> str | None:
    if kind in {'checkbox','radio','select'}: return None
    value = profile.get(kind)
    if value is None: return None
    min_len = int(field.get('minlength') or 0)
    max_len = int(field.get('maxlength') or 0)
    if max_len and len(value) > max_len: value = value[:max_len]
    if min_len and len(value) < min_len: value = value + ('0' * (min_len - len(value)))
    if kind == 'number':
        minimum = field.get('min'); maximum = field.get('max')
        try:
            number = float(value)
            if minimum is not None: number = max(number, float(minimum))
            if maximum is not None: number = min(number, float(maximum))
            value = str(int(number)) if number.is_integer() else str(number)
        except (TypeError, ValueError): pass
    if kind == 'date':
        if field.get('min') and value < field['min']: value = field['min']
        if field.get('max') and value > field['max']: value = field['max']
    return value
