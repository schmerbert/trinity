-- Trinity security fixes — run once in Supabase SQL editor
-- Addresses two warnings from the Supabase security linter.

-- 1. Fix search_shelf function: lock the search_path so it can't be
--    manipulated by an attacker to redirect function calls.
ALTER FUNCTION public.search_shelf(uuid, vector, int, text)
SET search_path = public;

-- 2. Revoke anonymous and authenticated execute on rls_auto_enable.
--    This function should not be callable via the public REST API.
REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM anon, authenticated;
