// GorgonMetaHuman.h — module shim for the FaceContract v1.4.1 UE5 client.
#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

class FGorgonMetaHumanModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;
};
