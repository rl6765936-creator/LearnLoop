"""Transfer Client (B7). Turns filtered outbox entries into TransferEnvelopes and hands them
to a Transport. REAL network transfer is NOT implemented: only a dry run (shows what would
be sent) and a simulation transport exist, until the transfer design is approved (Q-D)."""
