-- Целевой BSR по каждому ASIN списка (SOP-AMZ-001, шаг 2: BSR после восстановления, например 55 000–60 000).
-- NULL — брать текущий BSR товара. Поиск по списку копирует цель в строку searches.target_bsr.
-- Дашборд применяет эту строку сам при открытии режима «Список ASIN»; повторный запуск безопасен.
ALTER TABLE best_seller.asins ADD COLUMN IF NOT EXISTS target_bsr INTEGER
    CHECK (target_bsr IS NULL OR target_bsr > 0);
