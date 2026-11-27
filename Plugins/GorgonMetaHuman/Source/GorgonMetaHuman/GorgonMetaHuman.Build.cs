using UnrealBuildTool;

public class GorgonMetaHuman : ModuleRules
{
    public GorgonMetaHuman(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "WebSockets",       // RFC-6455 client (the FaceContract data plane)
            "Json",
            "JsonUtilities"
        });

        // No MetaHuman / ControlRig module dependencies on purpose: the
        // retarget component drives plain ARKit-named morph targets on any
        // USkeletalMeshComponent (RETARGETING.md §2/§4 discipline). A
        // MetaHuman face mesh is just the most complete ARKit-named rig you
        // can point at it; the tongue Control-Rig curve mapping is DEFERRED
        // until a real rig commits it (metahuman_target stays null in
        // channels.json).
    }
}
