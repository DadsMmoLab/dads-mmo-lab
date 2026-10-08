-- Unbound Wrath Edition — add missing druid shapeshift forms to catalog
-- Bear Form was a class quest in vanilla, so the Playerbots trainer
-- template (200006) never included it.  Add it manually at a custom price.
-- All other forms are already present from the Playerbots trainer data.

INSERT INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level)
VALUES (11, 5487, 500, 10)
ON DUPLICATE KEY UPDATE gold_cost_copper = 500, req_level = 10;
