SELECT setval(pg_get_serial_sequence('accounts',  'id'), COALESCE(MAX(id),1)) FROM accounts;
SELECT setval(pg_get_serial_sequence('orders',    'id'), COALESCE(MAX(id),1)) FROM orders;
SELECT setval(pg_get_serial_sequence('referrals', 'id'), COALESCE(MAX(id),1)) FROM referrals;
SELECT setval(pg_get_serial_sequence('tickets',   'id'), COALESCE(MAX(id),1)) FROM tickets;
SELECT setval(pg_get_serial_sequence('warranty',  'id'), COALESCE(MAX(id),1)) FROM warranty;