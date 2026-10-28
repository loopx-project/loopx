------------------------ MODULE TurnRecovery ------------------------
EXTENDS Naturals, FiniteSets, TLC
CONSTANT GuardUnknown
VARIABLES phase, owner, admitted, live, attempts, effects, settlements, failures, authorized
vars == <<phase, owner, admitted, live, attempts, effects, settlements, failures, authorized>>
Init == /\ phase = "new" /\ owner = 0 /\ admitted = {} /\ live = {}
        /\ attempts = 0 /\ effects = 0 /\ settlements = 0
        /\ failures = 0 /\ authorized = TRUE
Admit(e) == /\ phase = "new" /\ owner = 0 /\ admitted = {} /\ authorized
            /\ owner' = e /\ admitted' = admitted \cup {e} /\ live' = live \cup {e}
            /\ UNCHANGED <<phase, attempts, effects, settlements, failures, authorized>>
Dispatch == /\ phase = "new" /\ owner # 0 /\ authorized
            /\ phase' = "intent" /\ attempts' = attempts + 1
            /\ UNCHANGED <<owner, admitted, live, effects, settlements, failures, authorized>>
Tool == /\ phase = "intent" /\ owner # 0 /\ authorized /\ effects < attempts
        /\ effects' = effects + 1
        /\ UNCHANGED <<phase, owner, admitted, live, attempts, settlements, failures, authorized>>
SaveResult == /\ phase = "intent" /\ owner # 0 /\ authorized /\ effects = attempts
              /\ phase' = "result"
              /\ UNCHANGED <<owner, admitted, live, attempts, effects, settlements, failures, authorized>>
Crash == /\ owner # 0 /\ phase \in {"new", "intent", "result"} /\ failures < 2
         /\ owner' = 0 /\ admitted' = {} /\ failures' = failures + 1
         /\ UNCHANGED <<phase, live, attempts, effects, settlements, authorized>>
Recover == /\ owner = 0 /\ phase \in {"intent", "result"}
           /\ phase' = IF phase = "result" THEN "result"
                      ELSE IF GuardUnknown THEN "unknown" ELSE "new"
           /\ live' = {} /\ owner' = IF phase = "result" THEN 1 ELSE 0
           /\ admitted' = IF phase = "result" THEN {1} ELSE {}
           /\ UNCHANGED <<attempts, effects, settlements, failures, authorized>>
Settle == /\ phase = "result" /\ owner # 0 /\ authorized
          /\ phase' = "settled" /\ settlements' = 1 /\ owner' = 0 /\ admitted' = {} /\ live' = {}
          /\ UNCHANGED <<attempts, effects, failures, authorized>>
Revoke == /\ authorized /\ phase \notin {"settled", "unknown", "revoked"}
          /\ authorized' = FALSE /\ phase' = "revoked" /\ owner' = 0 /\ admitted' = {} /\ live' = {}
          /\ UNCHANGED <<attempts, effects, settlements, failures>>
Advance == (\E e \in {1,2}: Admit(e)) \/ Dispatch \/ Tool \/ SaveResult \/ Settle
Next == Advance \/ Crash \/ Recover \/ Revoke
Spec == Init /\ [][Next]_vars /\ WF_vars(Advance) /\ WF_vars(Recover)
OneAuthority == /\ Cardinality(admitted) <= 1
                /\ (owner = 0 <=> admitted = {})
                /\ (owner # 0 => admitted = {owner})
NoReplacement == attempts <= 1
NoDuplicateEffect == effects <= 1
AtMostOneSettlement == settlements <= 1
NoUnauthorizedAcceptance == ~authorized => settlements = 0
Terminates == <> (phase \in {"settled", "unknown", "revoked"})
=============================================================================
