// GorgonMetaHumanActor.cpp — see header.
#include "GorgonMetaHumanActor.h"

#include "Components/SceneComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "GorgonWebSocketClient.h"
#include "MetaHumanRetargetComponent.h"

AGorgonMetaHumanActor::AGorgonMetaHumanActor()
{
    PrimaryActorTick.bCanEverTick = false;

    Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    SetRootComponent(Root);

    RetargetComponent = CreateDefaultSubobject<UMetaHumanRetargetComponent>(TEXT("RetargetComponent"));
}

void AGorgonMetaHumanActor::BeginPlay()
{
    Super::BeginPlay();

    if (MetaHumanFaceMesh != nullptr)
    {
        RetargetComponent->SetTargetMesh(MetaHumanFaceMesh);
    }

    WebSocketClient = NewObject<UGorgonWebSocketClient>(this, TEXT("GorgonWebSocketClient"));
    WebSocketClient->OnFrameReceived.AddUniqueDynamic(this, &AGorgonMetaHumanActor::HandleFrameReceived);
    WebSocketClient->OnTriggerReceived.AddUniqueDynamic(this, &AGorgonMetaHumanActor::HandleTriggerReceived);
    WebSocketClient->OnConnectionStateChanged.AddUniqueDynamic(this, &AGorgonMetaHumanActor::HandleConnectionStateChanged);

    if (bAutoConnect)
    {
        WebSocketClient->Connect(WebSocketUrl, BearerToken);
    }
}

void AGorgonMetaHumanActor::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    if (WebSocketClient != nullptr)
    {
        WebSocketClient->Disconnect();
        WebSocketClient = nullptr;
    }
    Super::EndPlay(EndPlayReason);
}

void AGorgonMetaHumanActor::HandleFrameReceived(const FFaceContractFrame& Frame)
{
    RetargetComponent->ApplyFrame(Frame);
}

void AGorgonMetaHumanActor::HandleTriggerReceived(const FString& Reaction)
{
    UE_LOG(LogTemp, Log, TEXT("Gorgon trigger: %s"), *Reaction);
    OnTriggerReceivedBP(Reaction);
}

void AGorgonMetaHumanActor::HandleConnectionStateChanged(bool bConnected, const FString& Reason)
{
    if (bConnected)
    {
        // §6.3: on (re)connect, fade in from the last rendered pose.
        RetargetComponent->PrepareFadeIn();
    }
}
