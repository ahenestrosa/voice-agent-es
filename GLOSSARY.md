# Banco Austral phone support

A voice agent that answers phone calls for a fake Argentine bank, and the fake core-banking system its tools read and change.

## Language

### People and calls

**Caller**:
The person speaking on the phone. A Caller is unknown until Verification links them to a Customer.
_Avoid_: User, client

**Customer**:
A person who holds an account and cards at the bank, identified by their DNI.
_Avoid_: Account holder, client, user

**Call**:
One phone conversation between a Caller and the agent, from the first message until hang-up. ElevenLabs calls it a conversation.
_Avoid_: Conversation (outside ElevenLabs APIs), chat

### Identity

**Verification**:
The check that a Caller is a Customer: their DNI matches, and they hold a card ending in the digits they gave.
_Avoid_: Login, authentication

**Session**:
The permission a successful Verification grants: during one Call, the Caller may act on that one Customer's account. It ends when the Call ends.
_Avoid_: Login session, token (the token is how a Session is referred to, not the Session itself)
