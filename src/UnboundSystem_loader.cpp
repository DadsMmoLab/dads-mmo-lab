// AzerothCore module loader — registers AddUnboundScripts() with the engine.
// AzerothCore generates one call, Addmod_unboundScripts(), for the module folder
// "mod-unbound"; this file defines it. The bundled mod-multiclass-summons sources sit
// under src/mod-multiclass-summons/ and are compiled with this module, but AzerothCore
// would only look for Addmod_multiclass_summonsScripts() in a folder of that name, so
// it is called from here.

void AddUnboundScripts();
void AddUnboundMulticlassBridge();
void Addmod_multiclass_summonsScripts();

void Addmod_unboundScripts()
{
    AddUnboundScripts();
    AddUnboundMulticlassBridge();
    Addmod_multiclass_summonsScripts();
}
