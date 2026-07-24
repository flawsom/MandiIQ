-- 04: Price dispersion (max-min spread) by market and commodity
-- Shows: which markets have the widest price variation
--
-- Uses: window functions on aggregated market-level data
-- Pattern: GROUP BY market → window function for ranking

WITH market_stats AS (
    SELECT
        state,
        market,
        commodity,
        AVG(modal_price) AS avg_price,
        STDDEV(modal_price) AS price_std,
        MAX(modal_price) - MIN(modal_price) AS price_range,
        MIN(modal_price) AS min_price,
        MAX(modal_price) AS max_price,
        COUNT(*) AS n_observations
    FROM prices
    WHERE modal_price IS NOT NULL AND commodity = ?
    GROUP BY state, market, commodity
    HAVING COUNT(*) >= 10
)
SELECT
    state,
    market,
    commodity,
    ROUND(avg_price, 2) AS avg_price,
    ROUND(price_std, 2) AS price_std,
    ROUND(price_range, 2) AS price_range,
    ROUND(min_price, 2) AS min_price,
    ROUND(max_price, 2) AS max_price,
    ROUND((max_price - min_price) / NULLIF(avg_price, 0) * 100, 1) AS dispersion_pct,
    n_observations,
    RANK() OVER (ORDER BY price_range DESC) AS dispersion_rank
FROM market_stats
ORDER BY dispersion_rank;
