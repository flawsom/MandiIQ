-- 05: Year-over-year rainfall deficiency comparison by district
-- Shows: how this year's deficiency months compare to the same period last year
-- and whether prices responded differently
--
-- Uses: CTEs, LAG window function for YoY comparison
-- Pattern: self-join via window function, not subquery

WITH monthly_price_rainfall AS (
    SELECT
        p.state,
        p.district,
        EXTRACT(YEAR FROM p.arrival_date) AS year,
        EXTRACT(MONTH FROM p.arrival_date) AS month,
        AVG(p.modal_price) AS avg_price,
        AVG(r.departure_pct) AS avg_departure
    FROM prices p
    JOIN district_map dm ON p.state = dm.state AND p.district = dm.district
    JOIN rainfall r ON dm.sub_division = r.sub_division
        AND EXTRACT(YEAR FROM p.arrival_date) = r.year
        AND EXTRACT(MONTH FROM p.arrival_date) = r.month
    WHERE p.modal_price IS NOT NULL AND r.departure_pct IS NOT NULL
        AND p.commodity = ?
    GROUP BY p.state, p.district, EXTRACT(YEAR FROM p.arrival_date),
             EXTRACT(MONTH FROM p.arrival_date)
)
SELECT
    state,
    district,
    year,
    month,
    ROUND(avg_price, 2) AS avg_price,
    ROUND(avg_departure, 1) AS avg_departure,
    CASE WHEN avg_departure < -19 THEN 'Deficient' ELSE 'Normal' END AS rainfall_status,
    ROUND(LAG(avg_price, 12) OVER (
        PARTITION BY state, district ORDER BY year, month
    ), 2) AS price_same_month_last_year,
    ROUND(LAG(avg_departure, 12) OVER (
        PARTITION BY state, district ORDER BY year, month
    ), 1) AS departure_same_month_last_year,
    CASE WHEN avg_departure < -19 AND LAG(avg_departure, 12) OVER (
        PARTITION BY state, district ORDER BY year, month
    ) >= -19 THEN 'Newly deficient'
         WHEN avg_departure >= -19 AND LAG(avg_departure, 12) OVER (
        PARTITION BY state, district ORDER BY year, month
    ) < -19 THEN 'Recovered'
         ELSE 'Stable' END AS status_change
FROM monthly_price_rainfall
ORDER BY state, district, year DESC, month DESC;
