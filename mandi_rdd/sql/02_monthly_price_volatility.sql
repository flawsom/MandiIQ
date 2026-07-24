-- 02: Month-over-month price volatility by commodity
-- Uses: CTE for monthly aggregation, volatility = (max - min) / avg
--
-- Pattern: CTE → window function on CTE → ordered output
-- Mirrors Superstore's cohort-style analysis, applied to live-refreshing data

WITH monthly_prices AS (
    SELECT
        commodity,
        state,
        district,
        DATE_TRUNC('month', arrival_date) AS month,
        AVG(modal_price) AS avg_price,
        MAX(modal_price) - MIN(modal_price) AS price_spread,
        COUNT(*) AS trading_days
    FROM prices
    WHERE modal_price IS NOT NULL
    GROUP BY commodity, state, district, DATE_TRUNC('month', arrival_date)
    HAVING COUNT(*) >= 5  -- Minimum trading days for reliable monthly stats
)
SELECT
    commodity,
    state,
    month,
    avg_price,
    price_spread,
    price_spread / NULLIF(avg_price, 0) * 100 AS volatility_pct,
    trading_days,
    AVG(avg_price) OVER (
        PARTITION BY commodity, state
        ORDER BY month
        ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING
    ) AS prior_3mo_avg
FROM monthly_prices
WHERE commodity = ?  -- Parameter: commodity filter
ORDER BY commodity, state, month DESC;
