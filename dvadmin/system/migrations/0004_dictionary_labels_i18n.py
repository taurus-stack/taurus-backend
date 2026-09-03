"""Migrate built-in dictionary labels to i18n keys.

Changes dictionary items whose labels are hard-coded Chinese to stable i18n
keys of the form ``dict.xxx``. The frontend matches labels starting with
``dict.`` and translates them through the vue-i18n layer, so the same
database row renders correctly in zh-cn / en / zh-tw locales.

Only built-in dictionaries are touched (button_status_bool,
button_status_number, button_whether_bool, button_whether_number, gender).
Custom dictionaries added by users are left intact.
"""
from django.db import migrations


# Map: (parent_dictionary_value, old_label, new_value) -> new_label
LABEL_MAP_BY_PARENT_AND_VALUE = {
    # button_status_bool (boolean: "true" / "false")
    ("button_status_bool", "true"): "dict.enable",
    ("button_status_bool", "false"): "dict.disable",
    # button_status_number (number: 1 / 0)
    ("button_status_number", "1"): "dict.enable",
    ("button_status_number", "0"): "dict.disable",
    # button_whether_bool (boolean)
    ("button_whether_bool", "true"): "dict.yes",
    ("button_whether_bool", "false"): "dict.no",
    # button_whether_number (number: 1 / 2)
    ("button_whether_number", "1"): "dict.yes",
    ("button_whether_number", "2"): "dict.no",
    # gender (number)
    ("gender", "0"): "dict.genderUnknown",
    ("gender", "1"): "dict.genderMale",
    ("gender", "2"): "dict.genderFemale",
}


def update_dict_labels(apps, schema_editor):
    Dictionary = apps.get_model("system", "Dictionary")
    db_alias = schema_editor.connection.alias

    # Build lookup of parent dict id -> parent value (for top-level dicts with parent=None)
    parent_ids = {
        d["id"]: d["value"]
        for d in Dictionary.objects.using(db_alias)
        .filter(parent__isnull=True, value__in=list({k[0] for k in LABEL_MAP_BY_PARENT_AND_VALUE}))
        .values("id", "value")
    }

    items = (
        Dictionary.objects.using(db_alias)
        .filter(parent_id__in=parent_ids.keys(), is_value=True)
        .only("id", "parent_id", "value", "label")
    )

    updated = 0
    for item in items:
        parent_value = parent_ids.get(item.parent_id)
        key = (parent_value, str(item.value) if item.value is not None else "")
        new_label = LABEL_MAP_BY_PARENT_AND_VALUE.get(key)
        if not new_label:
            continue
        # Avoid rewriting rows that already match the target label
        if item.label == new_label:
            continue
        # Only rewrite known old Chinese labels (or if already dict.* keep them)
        if item.label and item.label.startswith("dict."):
            continue
        item.label = new_label
        item.save(update_fields=["label"])
        updated += 1
    print(f"[dict-i18n] Updated {updated} dictionary item labels to i18n keys.")


def reverse_update_dict_labels(apps, schema_editor):
    """Reverse migration: translate i18n keys back to zh-cn labels.

    This keeps the database row human-readable in Chinese environments, but
    loses the ability to switch language display. Safe to run as a rollback.
    """
    REVERSE_MAP = {
        "dict.enable": "启用",
        "dict.disable": "禁用",
        "dict.yes": "是",
        "dict.no": "否",
        "dict.genderUnknown": "未知",
        "dict.genderMale": "男",
        "dict.genderFemale": "女",
    }
    Dictionary = apps.get_model("system", "Dictionary")
    db_alias = schema_editor.connection.alias
    items = Dictionary.objects.using(db_alias).filter(label__startswith="dict.").only("id", "label")
    updated = 0
    for item in items:
        new_label = REVERSE_MAP.get(item.label)
        if not new_label:
            continue
        item.label = new_label
        item.save(update_fields=["label"])
        updated += 1
    print(f"[dict-i18n-reverse] Restored {updated} dictionary item labels to zh-cn.")


class Migration(migrations.Migration):

    dependencies = [
        ("system", "0003_userpreference_userfavorite"),
    ]

    operations = [
        migrations.RunPython(update_dict_labels, reverse_update_dict_labels),
    ]
