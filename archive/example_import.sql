-- Example import file
-- Generated: 2026-01-31

-- 1. Create a fixture
-- Note: 'games' must be newline-separated
INSERT INTO fixtures (week_number, games, deadline, status, created_at)
VALUES (1, 'Team A - Team B
Team C - Team D
Team E - Team F', '2026-02-01 18:00:00', 'open', '2026-01-31 12:00:00');

-- 2. Add predictions
-- Note: 'predictions' must match the number of games in the fixture
-- Use fixture_id = 1 (if this is the first import)
INSERT INTO predictions (fixture_id, user_id, user_name, predictions, submitted_at, is_late)
VALUES (1, '123456789', 'ExampleUser', '2-1
1-1
0-2', '2026-01-31 17:00:00', 0);
