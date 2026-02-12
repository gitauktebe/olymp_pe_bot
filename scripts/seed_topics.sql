insert into public.topics (id, title, is_active)
values
  (1, 'Networking', true),
  (2, 'Linux', true),
  (3, 'Python', true),
  (4, 'Databases', true),
  (5, 'Security', true)
on conflict (id) do update set
  title = excluded.title,
  is_active = excluded.is_active;
