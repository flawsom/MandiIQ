-- Category and Sub-Category Profitability Analysis
-- CTE-based cohort analysis with category-level aggregates

WITH category_margins AS (
    SELECT 
        category,
        sub_category,
        COUNT(*) AS order_lines,
        ROUND(AVG(profit_margin), 2) AS avg_margin_pct,
        ROUND(SUM(profit), 2) AS total_profit,
        ROUND(SUM(sales), 2) AS total_sales,
        ROUND(AVG(discount) * 100, 1) AS avg_discount_pct,
        ROUND(SUM(CASE WHEN is_loss = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS loss_rate_pct
    FROM superstore_sales
    GROUP BY category, sub_category
),
category_summary AS (
    SELECT 
        category,
        COUNT(*) AS sub_category_count,
        ROUND(AVG(avg_margin_pct), 2) AS avg_category_margin,
        SUM(total_profit) AS category_total_profit,
        SUM(total_sales) AS category_total_sales,
        ROUND(AVG(avg_discount_pct), 1) AS avg_category_discount
    FROM category_margins
    GROUP BY category
)
SELECT 
    cm.category,
    cm.sub_category,
    cm.order_lines,
    cm.total_sales,
    cm.total_profit,
    cm.avg_margin_pct,
    cm.avg_discount_pct,
    cm.loss_rate_pct,
    cs.avg_category_margin,
    ROUND(cm.avg_margin_pct - cs.avg_category_margin, 2) AS vs_category_avg,
    ROW_NUMBER() OVER (PARTITION BY cm.category ORDER BY cm.avg_margin_pct ASC) AS worst_in_category
FROM category_margins cm
JOIN category_summary cs ON cm.category = cs.category
ORDER BY cm.avg_margin_pct ASC;
