-- Regional Cohort Margin Analysis
-- Uses window functions and CTEs to compare regional profitability

WITH regional_stats AS (
    SELECT 
        region,
        COUNT(*) AS order_count,
        ROUND(AVG(profit_margin), 2) AS avg_margin_pct,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY profit_margin), 2) AS median_margin_pct,
        ROUND(SUM(profit), 2) AS total_profit,
        ROUND(SUM(sales), 2) AS total_sales,
        ROUND(SUM(CASE WHEN is_loss = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS loss_rate_pct
    FROM superstore_sales
    GROUP BY region
),
regional_ranked AS (
    SELECT 
        *,
        ROW_NUMBER() OVER (ORDER BY avg_margin_pct DESC) AS margin_rank,
        ROUND(AVG(avg_margin_pct) OVER (), 2) AS overall_avg_margin,
        ROUND(avg_margin_pct - AVG(avg_margin_pct) OVER (), 2) AS margin_vs_overall
    FROM regional_stats
)
SELECT 
    region,
    order_count,
    avg_margin_pct,
    median_margin_pct,
    total_profit,
    total_sales,
    loss_rate_pct,
    margin_rank,
    overall_avg_margin,
    margin_vs_overall,
    CASE 
        WHEN margin_vs_overall > 5 THEN 'Above Target'
        WHEN margin_vs_overall BETWEEN -5 AND 5 THEN 'At Target'
        ELSE 'Below Target'
    END AS performance_tier
FROM regional_ranked
ORDER BY margin_rank;
