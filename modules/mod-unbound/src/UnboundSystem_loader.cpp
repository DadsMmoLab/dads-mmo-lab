// AzerothCore module loader — registers AddUnboundScripts() with the engine.
// The top-level modules/CMakeLists.txt calls Addmod_unboundScripts(),
// which this file defines by forwarding to our actual registration function.

void AddUnboundScripts();

void Addmod_unboundScripts()
{
    AddUnboundScripts();
}
