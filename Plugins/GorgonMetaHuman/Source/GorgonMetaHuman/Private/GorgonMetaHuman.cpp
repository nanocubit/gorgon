// GorgonMetaHuman.cpp — module lifecycle.
#include "GorgonMetaHuman.h"

#define LOCTEXT_NAMESPACE "FGorgonMetaHumanModule"

void FGorgonMetaHumanModule::StartupModule()
{
    UE_LOG(LogTemp, Log, TEXT("GorgonMetaHuman module started"));
}

void FGorgonMetaHumanModule::ShutdownModule()
{
    UE_LOG(LogTemp, Log, TEXT("GorgonMetaHuman module stopped"));
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FGorgonMetaHumanModule, GorgonMetaHuman)
