// GorgonMetaHumanActor.h — minimal wiring: WS client → retarget component.
//
// Root is a plain USceneComponent: a UActorComponent can never be a root
// component (common draft mistake). Reaction semantics for bridge triggers
// are left to the designer via OnTriggerReceivedBP — the locked wire
// contract defines no trigger semantics (bridge extension only).
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "FaceContractDecoder.h"
#include "GorgonMetaHumanActor.generated.h"

class UGorgonWebSocketClient;
class UMetaHumanRetargetComponent;
class USceneComponent;
class USkeletalMeshComponent;

UCLASS(Blueprintable)
class GORGONMETAHUMAN_API AGorgonMetaHumanActor : public AActor
{
    GENERATED_BODY()

public:
    AGorgonMetaHumanActor();

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Gorgon")
    USceneComponent* Root;

    /** Face skeletal mesh with ARKit-named morph targets (e.g. MetaHuman). */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Gorgon")
    USkeletalMeshComponent* MetaHumanFaceMesh;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Gorgon")
    UMetaHumanRetargetComponent* RetargetComponent;

    /** gorgon_ws_bridge.py endpoint. */
    UPROPERTY(EditAnywhere, Category="Gorgon")
    FString WebSocketUrl = TEXT("ws://127.0.0.1:8765");

    /** Optional; the local bridge needs none. */
    UPROPERTY(EditAnywhere, Category="Gorgon")
    FString BearerToken;

    UPROPERTY(EditAnywhere, Category="Gorgon")
    bool bAutoConnect = true;

private:
    /** Created at runtime, kept referenced — GC-safe. */
    UPROPERTY(Transient)
    UGorgonWebSocketClient* WebSocketClient;

    UFUNCTION()
    void HandleFrameReceived(const FFaceContractFrame& Frame);

    UFUNCTION()
    void HandleTriggerReceived(const FString& Reaction);

    UFUNCTION()
    void HandleConnectionStateChanged(bool bConnected, const FString& Reason);

    /** Designer hook for bridge control events ({"reaction": ...}). */
    UFUNCTION(BlueprintImplementableEvent, Category="Gorgon", meta=(DisplayName="On Trigger Received"))
    void OnTriggerReceivedBP(const FString& Reaction);
};
