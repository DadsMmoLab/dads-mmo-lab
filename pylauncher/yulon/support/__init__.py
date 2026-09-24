"""Support files (T93): what a player sends us when something goes wrong.

One rule governs the whole package: nothing read here leaves the machine
unredacted. Files on disk stay raw -- they are our evidence -- and every
viewer and every zip passes through `redact.Redactor` on the way out.
"""
