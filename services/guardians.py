"""Guardian form parsing and validation independent of image enrollment."""
import re
import unicodedata

RELATIONS = ('father', 'mother', 'grandmother', 'grandfather', 'guardian', 'other')
FIELDS = ('relation_type', 'last_name', 'first_name', 'middle_name', 'phone')
MAX_GUARDIANS = 10


def clean_text(value, limit, required=False):
    value = value.strip()
    if (required and not value) or len(value) > limit or any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('invalid_form')
    return value


def form_rows(form):
    indices = form.getlist('guardian_index')
    if len(indices) > MAX_GUARDIANS or len(set(indices)) != len(indices) or any(not re.fullmatch(r'[0-9]{1,6}', i) for i in indices):
        raise ValueError('invalid_form')
    return [dict({field: form.get(f'guardian_{i}_{field}', '').strip() for field in FIELDS},
                 is_primary=form.get(f'guardian_{i}_is_primary') == 'on') for i in indices]


def validate(rows):
    if sum(row['is_primary'] for row in rows) > 1:
        raise ValueError('one_primary')
    for row in rows:
        if row['relation_type'] not in RELATIONS:
            raise ValueError('invalid_form')
        for name in ('first_name', 'last_name', 'middle_name'):
            clean_text(row[name], 80)
        # Partial old contacts are valid, but entirely empty rows are not.
        if not any(row[name] for name in ('first_name', 'last_name', 'middle_name', 'phone')):
            raise ValueError('invalid_form')
        if row['phone'] and not re.fullmatch(r'\+?[0-9() .-]{5,30}', row['phone']):
            raise ValueError('invalid_form')
    return rows


def snapshot(guardians):
    return [dict({field: getattr(g, field) or '' for field in FIELDS}, is_primary=g.is_primary) for g in guardians]
