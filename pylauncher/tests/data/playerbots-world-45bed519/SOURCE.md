cmangos/playerbots at 45bed519, sql/world, sql/world/tbc and sql/world/classic (the files TBC
and Vanilla load). Each file is VERBATIM statement by statement -- mysqldump headers,
executable comments, DROP/CREATE, comments -- except that every INSERT is cut to its first
row, which keeps the files small (100 KB for 32 MB). Built for T534's cold review: the
whole-table guard and the schema scan give these copies the same answer as the full files.
