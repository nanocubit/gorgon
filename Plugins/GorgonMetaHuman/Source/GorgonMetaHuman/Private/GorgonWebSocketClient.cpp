// GorgonWebSocketClient.cpp — plain WebSocket client (see header).
#include "GorgonWebSocketClient.h"

#include "Dom/JsonObject.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "WebSocketsModule.h"

void UGorgonWebSocketClient::Connect(const FString& Url, const FString& BearerToken)
{
    if (WebSocket.IsValid())
    {
        UE_LOG(LogTemp, Warning, TEXT("Gorgon WS: already connected/connecting"));
        return;
    }

    FWebSocketsModule& WSM = FModuleManager::LoadModuleChecked<FWebSocketsModule>(TEXT("WebSockets"));
    WebSocket = WSM.CreateWebSocket(Url);

    // AddUObject bindings are weak: they never fire on a GC'd object.
    WebSocket->OnConnected().AddUObject(this, &UGorgonWebSocketClient::HandleConnected);
    WebSocket->OnConnectionError().AddUObject(this, &UGorgonWebSocketClient::HandleConnectionError);
    WebSocket->OnClosed().AddUObject(this, &UGorgonWebSocketClient::HandleClosed);
    WebSocket->OnMessage().AddUObject(this, &UGorgonWebSocketClient::HandleTextMessage);
    WebSocket->OnBinaryMessage().AddUObject(this, &UGorgonWebSocketClient::HandleBinaryMessage);

    TMap<FString, FString> Headers;
    if (!BearerToken.IsEmpty())
    {
        Headers.Add(TEXT("Authorization"), FString::Printf(TEXT("Bearer %s"), *BearerToken));
    }

    UE_LOG(LogTemp, Log, TEXT("Gorgon WS: connecting to %s"), *Url);
    WebSocket->Connect(Headers);
}

void UGorgonWebSocketClient::Disconnect()
{
    if (WebSocket.IsValid())
    {
        WebSocket->OnConnected().RemoveAll(this);
        WebSocket->OnConnectionError().RemoveAll(this);
        WebSocket->OnClosed().RemoveAll(this);
        WebSocket->OnMessage().RemoveAll(this);
        WebSocket->OnBinaryMessage().RemoveAll(this);
        WebSocket->Close();
        WebSocket.Reset();
    }
    BinaryBuffer.Reset();
    SetConnected(false, TEXT("disconnect"));
}

void UGorgonWebSocketClient::BeginDestroy()
{
    if (WebSocket.IsValid())
    {
        WebSocket->OnConnected().RemoveAll(this);
        WebSocket->OnConnectionError().RemoveAll(this);
        WebSocket->OnClosed().RemoveAll(this);
        WebSocket->OnMessage().RemoveAll(this);
        WebSocket->OnBinaryMessage().RemoveAll(this);
        WebSocket->Close();
        WebSocket.Reset();
    }
    Super::BeginDestroy();
}

void UGorgonWebSocketClient::SetConnected(bool bNewState, const FString& Reason)
{
    if (bConnected == bNewState)
    {
        return;
    }
    bConnected = bNewState;
    OnConnectionStateChanged.Broadcast(bConnected, Reason);
}

void UGorgonWebSocketClient::HandleConnected()
{
    UE_LOG(LogTemp, Log, TEXT("Gorgon WS: connected"));
    SetConnected(true, TEXT(""));
}

void UGorgonWebSocketClient::HandleConnectionError(const FString& Error)
{
    UE_LOG(LogTemp, Error, TEXT("Gorgon WS: connection error: %s"), *Error);
    WebSocket.Reset();
    SetConnected(false, Error);
}

void UGorgonWebSocketClient::HandleClosed(int32 StatusCode, const FString& Reason, bool bWasClean)
{
    UE_LOG(LogTemp, Log, TEXT("Gorgon WS: closed %d (clean=%d): %s"),
           StatusCode, bWasClean ? 1 : 0, *Reason);
    WebSocket.Reset();
    SetConnected(false, Reason);
}

void UGorgonWebSocketClient::HandleTextMessage(const FString& Message)
{
    ProcessTrigger(Message);
}

void UGorgonWebSocketClient::HandleBinaryMessage(const void* Data, int64 Size, bool bIsLast)
{
    if (Data != nullptr && Size > 0)
    {
        BinaryBuffer.Append(static_cast<const uint8*>(Data), static_cast<int32>(Size));
    }
    if (!bIsLast)
    {
        return;                                   // fragmented — keep accumulating
    }

    TArray<uint8> Frame;
    Frame.Swap(BinaryBuffer);                     // take the buffer, leave it empty

    FFaceContractFrame Decoded;
    FString Error;
    if (FFaceContractDecoder::Decode(Frame, Decoded, &Error))
    {
        OnFrameReceived.Broadcast(Decoded);
    }
    else
    {
        UE_LOG(LogTemp, Warning, TEXT("Gorgon WS: frame rejected (%d bytes): %s"),
               Frame.Num(), *Error);
    }
}

void UGorgonWebSocketClient::ProcessTrigger(const FString& Json)
{
    TSharedPtr<FJsonObject> Obj;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
    if (!FJsonSerializer::Deserialize(Reader, Obj) || !Obj.IsValid())
    {
        return;
    }
    FString Type;
    if (!Obj->TryGetStringField(TEXT("type"), Type) || Type != TEXT("trigger_reaction"))
    {
        return;
    }
    FString Reaction;
    if (Obj->TryGetStringField(TEXT("reaction"), Reaction))
    {
        OnTriggerReceived.Broadcast(Reaction);
    }
}
