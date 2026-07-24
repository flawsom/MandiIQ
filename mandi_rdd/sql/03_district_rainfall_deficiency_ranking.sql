-- 03: District ranking by rainfall-deficiency frequency
-- Shows: which districts most frequently experience deficient rainfall (departure < -19%)
-- and how their prices respond
--
-- Uses: CTEs, window ranking, conditional aggregation
-- Pattern: join prices → rainfall on district/sub-division + year + month

WITH district_rainfall AS (
    SELECT
        p.state,
        p.district,
        p.commodity,
        r.year,
        r.month,
        r.departure_pct,
        r.rainfall_mm,
        r.normal_mm,
        AVG(p.modal_price) AS avg_modal_price,
        CASE WHEN r.departure_pct < -19 THEN 1 ELSE 0 END AS is_deficient
    FROM prices p
    JOIN district_map dm ON p.state = dm.state AND p.district = dm.district
    JOIN rainfall r ON dm.sub_division = r.sub_division
        AND EXTRACT(YEAR FROM p.arrival_date) = r.year
        AND EXTRACT(MONTH FROM p.arrival_date) = r.month
    WHERE p.modal_price IS NOT NULL AND r.departure_pct IS NOT NULL
    GROUP BY p.state, p.district, p.commodity, r.year, r.month,
             r.departure_pct, r.rainfall_mm, r.normal_mm
)
SELECT
    state,
    district,
    commodity,
    COUNT(*) AS total_months,
    SUM(is_deficient) AS deficient_months,
    ROUND(SUM(is_deficient) * 100.0 / COUNT(*), 1) AS pct_deficient,
    AVG(CASE WHEN is_deficient = 1 THEN avg_modal_price END) AS avg_price_deficient,
    AVG(CASE WHEN is_deficient = 0 THEN avg_modal_price END) AS avg_price_normal,
    RANK() OVER (PARTITION BY commodity ORDER BY SUM(is_deficient) DESC) AS deficiency_rank
FROM district_rainfall
WHERE commodity = ?  -- Parameter: commodity filter
GROUP BY state, district, commodity
HAVING COUNT(*) >= 6
ORDER BY deficiency_rank;
