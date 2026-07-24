-- Customer Segment Profitability Analysis
-- Combines region and segment dimensions with window functions

WITH segment_stats AS (
    SELECT 
        segment,
        region,
        COUNT(*) AS order_count,
        ROUND(SUM(sales), 2) AS total_sales,
        ROUND(SUM(profit), 2) AS total_profit,
        ROUND(AVG(profit_margin), 2) AS avg_margin_pct,
        ROUND(AVG(discount) * 100, 1) AS avg_discount_pct,
        ROUND(SUM(CASE WHEN is_loss = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS loss_rate_pct
    FROM superstore_sales
    GROUP BY segment, region
),
segment_ranking AS (
    SELECT 
        segment,
        region,
        order_count,
        total_sales,
        total_profit,
        avg_margin_pct,
        avg_discount_pct,
        loss_rate_pct,
        RANK() OVER (PARTITION BY segment ORDER BY avg_margin_pct DESC) AS best_region_rank,
        RANK() OVER (PARTITION BY region ORDER BY avg_margin_pct DESC) AS best_segment_rank
    FROM segment_stats
)
SELECT 
    segment,
    region,
    order_count,
    total_sales,
    total_profit,
    avg_margin_pct,
    avg_discount_pct,
    loss_rate_pct,
    best_region_rank,
    best_segment_rank,
    CASE 
        WHEN best_region_rank = 1 THEN 'Top Region'
        WHEN best_region_rank = 2 THEN 'Strong'
        ELSE 'Needs Review'
    END AS region_performance
FROM segment_ranking
ORDER BY segment, avg_margin_pct DESC;
