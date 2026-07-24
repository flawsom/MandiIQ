-- 01: Rolling 30-day price trend by district
-- Shows: rolling average, min, max modal_price per district
-- Window function: AVG() OVER (ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)
--
-- Mirrors Superstore's running_total SQL pattern, applied to government API data

SELECT
    state,
    district,
    commodity,
    arrival_date,
    modal_price,
    AVG(modal_price) OVER (
        PARTITION BY state, district, commodity
        ORDER BY arrival_date
        ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
    ) AS rolling_30d_avg_price,
    MIN(modal_price) OVER (
        PARTITION BY state, district, commodity
        ORDER BY arrival_date
        ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
    ) AS rolling_30d_min_price,
    MAX(modal_price) OVER (
        PARTITION BY state, district, commodity
        ORDER BY arrival_date
        ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
    ) AS rolling_30d_max_price,
    COUNT(*) OVER (
        PARTITION BY state, district, commodity
        ORDER BY arrival_date
        ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
    ) AS days_in_window
FROM prices
WHERE commodity = ?  -- Parameter: commodity filter
ORDER BY state, district, arrival_date DESC;
